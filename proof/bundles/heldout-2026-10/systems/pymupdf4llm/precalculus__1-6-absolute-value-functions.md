# **1.6 Absolute Value Functions** 

## **Learning Objectives** 

In this section you will: 

- Graph an absolute value function. 

- Solve an absolute value equation. 

- Solve an absolute value inequality. 



Figure 1 Distances in deep space can be measured in all directions. As such, it is useful to consider distance in terms of absolute values. (credit: "s58y"/Flickr) 

Until the 1920s, the so-called spiral nebulae were believed to be clouds of dust and gas in our own galaxy, some tens of thousands of light years away. Then, astronomer Edwin Hubble proved that these objects are galaxies in their own right, at distances of millions of light years. Today, astronomers can detect galaxies that are billions of light years away. Distances in the universe can be measured in all directions. As such, it is useful to consider distance as an absolute value function. In this section, we will investigate absolute value functions. 

## **Understanding Absolute Value** 

Recall that in its basic form 𝑓(𝑥) = |𝑥|, the absolute value function, is one of our toolkit functions. The absolute value function is commonly thought of as providing the distance the number is from zero on a number line. Algebraically, for whatever the input value is, the output is the value without regard to sign. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 1 of 28 

## **Absolute Value Function** 

The absolute value function can be defined as a piecewise function 

if 𝑥≥0 𝑓(𝑥) = |𝑥| = {<sup>𝑥</sup> −𝑥 if 𝑥< 0 

## **Example 1** 

### **Determine a Number within a Prescribed Distance** 

Describe all values 𝑥 within or including a distance of 4 from the number 5. 

### **Solution** 

We want the distance between 𝑥 and 5 to be less than or equal to 4. We can draw a number line, such as the one in Figure 2, to represent the condition to be satisfied. 



Figure 2 

The distance from 𝑥 to 5 can be represented using the absolute value as |𝑥−5| . We want the values of 𝑥 that satisfy the condition |𝑥−5| ≤4. 

### **Analysis** 

Note that −4 ≤𝑥−5 𝑥−5 ≤4 1 ≤𝑥 𝑥≤9 

So |𝑥−5| ≤4 is equivalent to 1 ≤𝑥≤9. However, mathematicians generally prefer absolute value notation. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 2 of 28 

## **Try It #1** 

Describe all values 𝑥 within a distance of 3 from the number 2. 

## **Example 2** 

### **Resistance of a Resistor** 

Electrical parts, such as resistors and capacitors, come with specified values of their operating parameters: resistance, capacitance, etc. However, due to imprecision in manufacturing, the actual values of these parameters vary somewhat from piece to piece, even when they are supposed to be the same. The best that manufacturers can do is to try to guarantee that the variations will stay within a specified range, often ±1%, ±5%, or ±10%. 

Suppose we have a resistor rated at 680 ohms, ±5% . Use the absolute value function to express the range of possible values of the actual resistance. 

### **Solution** 

5% of 680 ohms is 34 ohms. The absolute value of the difference between the actual and nominal 

resistance should not exceed the stated variability, so, with the resistance 𝑅 in ohms, 

|𝑅−680| ≤34 

## **Try It #2** 

Students who score within 20 points of 80 will pass a test. Write this as a distance from 80 using absolute value notation. 

## **Graphing an Absolute Value Function** 

The most significant feature of the absolute value graph is the corner point at which the graph changes direction. This point is shown at the origin in Figure 3. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 3 of 28 



Figure 3 

Figure 4 shows the graph of 𝑦= 2|𝑥 – 3| + 4. The graph of 𝑦= |𝑥| has been shifted right 3 units, vertically stretched by a factor of 2, and shifted up 4 units. This means that the corner point is located at (3, 4) for this transformed function. 



Figure 4 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 4 of 28 

## **Example 3** 

### **Writing an Equation for an Absolute Value Function** 

Write an equation for the function graphed in Figure 5. 



Figure 5 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 5 of 28 

### **Solution** 

The basic absolute value function changes direction at the origin, so this graph has been shifted to the right 3 units and down 2 units from the basic toolkit function. See Figure 6. 



Figure 6 

We also notice that the graph appears vertically stretched, because the width of the final graph on a horizontal line is not equal to 2 times the vertical distance from the corner to this line, as it would be for an unstretched absolute value function. Instead, the width is equal to 1 times the vertical distance as shown in Figure 7. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 6 of 28 



Figure 7 

From this information we can write the equation 

𝑓(𝑥) = 2 | 𝑥−3 | −2, treating the stretch as a vertical stretch, or 𝑓(𝑥) = | 2(𝑥−3) | −2, treating the stretch as a horizontal compression. 

### **Analysis** 

Note that these equations are algebraically equivalent—the stretch for an absolute value function can be written interchangeably as a vertical or horizontal stretch or compression. Note also that if the vertical stretch factor is negative, there is also a reflection about the x-axis. 

## **Q&A** 

**If we couldn’t observe the stretch of the function from the graphs, could we algebraically determine it?** 

_Yes. If we are unable to determine the stretch based on the width of the graph, we can solve for the stretch factor by putting in a known pair of values for_ 𝑥 _and_ 𝑓(𝑥) . 



_Now substituting in the point_ (1, 2) 



Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 7 of 28 

## **Try It #3** 

Write the equation for the absolute value function that is horizontally shifted left 2 units, is vertically flipped, and vertically shifted up 3 units. 

## **Q&A** 

**Do the graphs of absolute value functions always intersect the vertical axis? The horizontal axis?** 

_Yes, they always intersect the vertical axis. The graph of an absolute value function will intersect the vertical axis when the input is zero._ 

_No, they do not always intersect the horizontal axis. The graph may or may not intersect the horizontal axis, depending on how the graph has been shifted and reflected. It is possible for the absolute value function to intersect the horizontal axis at zero, one, or two points (see Figure 8)._ 



Figure 8 (a) The absolute value function does not intersect the horizontal axis. (b) The absolute value function intersects the horizontal axis at one point. (c) The absolute value function intersects the horizontal axis at two points. 

## **Solving an Absolute Value Equation** 

Now that we can graph an absolute value function, we will learn how to solve an absolute value equation. To solve an equation such as 8 = |2𝑥−6|, we notice that the absolute value will be equal to 8 if the quantity inside the absolute value is 8 or -8. This leads to two different equations we can solve independently. 



Knowing how to solve problems involving absolute value functions is useful. For example, we may need to identify numbers or points on a line that are at a specified distance from a given reference point. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 8 of 28 

An absolute value equation is an equation in which the unknown variable appears in absolute value bars. For example, 



|2𝑥−1| = 3 

|5𝑥+ 2| −4 = 9 

## **Solutions to Absolute Value Equations** 

For real numbers 𝐴 and 𝐵, an equation of the form |𝐴| = 𝐵, with 𝐵≥0, will have solutions when 𝐴= 𝐵 or 𝐴= −𝐵. If 𝐵< 0, the equation |𝐴| = 𝐵 has no solution. 

## **How To** 

**Given the formula for an absolute value function, find the horizontal intercepts of its graph** . 

1. Isolate the absolute value term. 

2. Use |𝐴| = 𝐵 to write 𝐴= 𝐵 or −A = 𝐵, assuming 𝐵> 0. 

3. Solve for 𝑥. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 9 of 28 

## **Example 4** 

### **Finding the Zeros of an Absolute Value Function** 

For the function 𝑓(𝑥) = |4𝑥+ 1| −7, find the values of 𝑥 such that 𝑓(𝑥) = 0. 

### **Solution** 



<!-- Start of picture text -->
0 = | 4𝑥+ 1 | −7 Substitute 0 for f(x).<br>7 = | 4𝑥+ 1| Isolate the absolute value on one side of the equation.<br>7 = 4𝑥+ 1 or −7 = 4𝑥+ 1 Break into two separate equations and solve.<br>6 = 4𝑥 −8 = 4𝑥<br>6 −8<br>𝑥= 4 = 1.5    𝑥= 4 = −2<br><!-- End of picture text -->

The function outputs 0 when 𝑥= 1.5 or 𝑥= −2. See Figure 9. 





<!-- Start of picture text -->
Figure 9<br><!-- End of picture text -->

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 10 of 28 

## **Try It #4** 

For the function 𝑓(𝑥) = |2𝑥−1| −3, find the values of 𝑥 such that 𝑓(𝑥) = 0. 

## **Q&A** 

**Should we always expect two answers when solving** |𝐴| = 𝐵? 

_No. We may find one, two, or even no answers. For example, there is no solution to_ 2 + |3𝑥−5| = 1. 

## **How To** 

#### **Given an absolute value equation, solve it.** 

1. Isolate the absolute value term. 

2. Use |𝐴| = 𝐵 to write 𝐴= 𝐵 or 𝐴= −B . 

3. Solve for 𝑥. 

## **Example 5** 

### **Solving an Absolute Value Equation** 

Solve 1 = 4|𝑥−2| + 2. 

### **Solution** 

Isolating the absolute value on one side of the equation gives the following. 

- 1 = 4|𝑥−2| + 2 

- −1 = 4|𝑥−2| 

- −1 4<sup>= |𝑥−2|</sup> 

The absolute value always returns a positive value, so it is impossible for the absolute value to equal a negative value. At this point, we notice that this equation has no solutions. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 11 of 28 

## **Q&A** 

**In Example 5, if** 𝑓(𝑥) = 1 **and** 𝑔(𝑥) = 4|𝑥−2| + 2 **were graphed on the same set of axes, would the graphs intersect?** 

_No. The graphs of_ 𝑓 _and_ 𝑔 _would not intersect, as shown in Figure 10. This confirms, graphically, that the equation_ 1 = 4|𝑥−2| + 2 _has no solution._ 



Figure 10 



<!-- Start of picture text -->
Try It #5<br>Find where the graph of the function 𝑓(𝑥) = −|𝑥+ 2| + 3 intersects the horizontal and vertical axes.<br><!-- End of picture text -->

## **Solving an Absolute Value Inequality** 

Absolute value equations may not always involve equalities. Instead, we may need to solve an equation within a range of values. We would use an absolute value inequality to solve such an equation. An absolute value inequality is an equation of the form 



where an expression 𝐴 (and possibly but not usually 𝐵 ) depends on a variable 𝑥. Solving the inequality means finding the set of all 𝑥 that satisfy the inequality. Usually this set will be an interval or the union of two intervals. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 12 of 28 

There are two basic approaches to solving absolute value inequalities: graphical and algebraic. The advantage of the graphical approach is we can read the solution by interpreting the graphs of two functions. The advantage of the algebraic approach is it yields solutions that may be difficult to read from the graph. 

For example, we know that all numbers within 200 units of 0 may be expressed as 



Suppose we want to know all possible returns on an investment if we could earn some amount of money within $200 of $600. We can solve algebraically for the set of values 𝑥 such that the distance between 𝑥 and 600 is less than 200. We represent the distance between 𝑥 and 600 as |𝑥−600| . 





This means our returns would be between $400 and $800. 

Sometimes an absolute value inequality problem will be presented to us in terms of a shifted and/or stretched or compressed absolute value function, where we must determine for which values of the input the function’s output will be negative or positive. 

## **How To** 

**Given an absolute value inequality of the form** |𝑥−𝐴| ≤𝐵 **for real numbers** 𝑎 **and** 𝑏 **where** 𝑏 **is positive, solve the absolute value inequality algebraically.** 

1. Find boundary points by solving |𝑥−𝐴| = 𝐵. 

2. Test intervals created by the boundary points to determine where |𝑥−𝐴| ≤𝐵. 

3. Write the interval or union of intervals satisfying the inequality in interval, inequality, or set-builder notation. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 13 of 28 

## **Example 6** 

### **Solving an Absolute Value Inequality** 

Solve |𝑥 −5 | < 4. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 14 of 28 

### **Solution** 

With both approaches, we will need to know first where the corresponding equality is true. In this case we first will find where |𝑥−5| = 4. We do this because the absolute value is a function with no breaks, so the only way the function values can switch from being less than 4 to being greater than 4 is by passing through where the values equal 4. Solve |𝑥−5| = 4. 



After determining that the absolute value is equal to 4 at 𝑥= 1 and 𝑥= 9, we know the graph can change only from being less than 4 to greater than 4 at these values. This divides the number line up into three intervals: 



To determine when the function is less than 4, we could choose a value in each interval and see if the output is less than or greater than 4, as shown in Table 1. 

|**Interval test**𝑥|𝑓(𝑥)|< 4**or**|> 4 ?|
|---|---|---|---|
|𝑥< 1|0||0 −5| = 5|Greater than|
|1 < 𝑥< 9|6||6 −5| = 1|Less than|
|𝑥> 9|11||11 −5| = 6|Greater than|



Table 1 

Because 1 < 𝑥< 9 is the only interval in which the output at the test value is less than 4, we can conclude that the solution to |𝑥−5| < 4 is 1 < 𝑥< 9, or (1, 9) . 

To use a graph, we can sketch the function 𝑓(𝑥) = |𝑥−5| . To help us see where the outputs are 4, the line 𝑔(𝑥) = 4 could also be sketched as in Figure 11. 



Figure 11 Graph to find the points satisfying an absolute value inequality. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 15 of 28 

We can see the following: 

The output values of the absolute value are equal to 4 at 𝑥= 1 and 𝑥= 9. 

The graph of 𝑓 is below the graph of 𝑔 on 1 < 𝑥< 9. This means the output values of 𝑓(𝑥) are less than the output values of 𝑔(𝑥) . 

The absolute value is less than or equal to 4 between these two points, when 1 < 𝑥< 9. In interval notation, this would be the interval (1, 9) . 

### **Analysis** 

For absolute value inequalities, 



The < or > symbol may be replaced by ≤ or ≥. 

So, for this example, we could use this alternative approach. 

| 𝑥−5 | < 4 −4 < 𝑥−5 < 4 Rewrite by removing the absolute value bars. −4 + 5 < 𝑥−5 + 5 < 4 + 5 Isolate the 𝑥. 1 < 𝑥< 9 

## **Try It #6** 

Solve |𝑥+ 2| ≤6. 

## **How To** 

**Given an absolute value function, solve for the set of inputs where the output is positive (or negative).** 

1. Set the function equal to zero, and solve for the boundary points of the solution set. 

2. Use test points or a graph to determine where the function’s output is positive or negative. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 16 of 28 

## **Example 7** 

**Using a Graphical Approach to Solve Absolute Value Inequalities** Given the function 𝑓(𝑥) = − <u>12</u><sup>|4𝑥−5| + 3,determine the 𝑥- values for which the function values are</sup> negative. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 17 of 28 

### **Solution** 

We are trying to determine where 𝑓(𝑥) < 0, which is when −12 | 4𝑥−5 | + 3 < 0. We begin by isolating the absolute value. 



Next we solve for the equality |4𝑥−5| = 6. 



Now, we can examine the graph of 𝑓 to observe where the output is negative. We will observe where the branches are below the _x_ -axis. Notice that it is not even important exactly what the graph looks like, as long as we know that it crosses the horizontal axis at 𝑥= − <u>14</u><sup>and 𝑥=</sup> <u>114</u><sup>and that the graph has been</sup> reflected vertically. See Figure 12 **.** 



Figure 12 

We observe that the graph of the function is below the _x_ -axis left of 𝑥= − <u>14</u><sup>and right of 𝑥=</sup> <u>114</u><sup>.This</sup> means the function values are negative to the left of the first horizontal intercept at 𝑥= − <u>14</u><sup>,and</sup> negative to the right of the second intercept at 𝑥= <u>114</u><sup>.This gives us the solution to the inequality.</sup> <u>1 11</u> 𝑥< − 4<sup>or 𝑥></sup> 4 

In interval notation, this would be (−∞, −0.25) ∪(2.75, ∞) . 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 18 of 28 

## **Try It #7** 

Solve −2|𝑘−4| ≤−6. 

## **Media** 

Access these online resources for additional instruction and practice with absolute value. 

- Graphing Absolute Value Functions 

- Graphing Absolute Value Functions 2 

- Equations of Absolute Value Function 

- Equations of Absolute Value Function 2 

- Solving Absolute Value Equations 

## **1.6 Section Exercises** 

### **Verbal** 

1. 

How do you solve an absolute value equation? 

2. 

How can you tell whether an absolute value function has two _x_ -intercepts without graphing the function? 

3. 

When solving an absolute value function, the isolated absolute value term is equal to a negative number. What does that tell you about the graph of the absolute value function? 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 19 of 28 

#### 4. 

How can you use the graph of an absolute value function to determine the _x_ -values for which the function values are negative? 

#### 5. 

How do you solve an absolute value inequality algebraically? 

### **Algebraic** 

#### 6. 

Describe all numbers 𝑥 that are at a distance of 4 from the number 8. Express this using absolute value notation. 

7. 

Describe all numbers 𝑥 that are at a distance of 12<sup>from the number −4. Express this using absolute value</sup> notation. 

#### 8. 

Describe the situation in which the distance that point 𝑥 is from 10 is at least 15 units. Express this using absolute value notation. 

#### 9. 

Find all function values 𝑓(𝑥) such that the distance from 𝑓(𝑥) to the value 8 is less than 0.03 units. Express this using absolute value notation. 

For the following exercises, solve the equations below and express the answer using set notation. 

10. |𝑥+ 3 | = 9 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 20 of 28 



<!-- Start of picture text -->
11.<br>|6 −𝑥| = 5<br>12.<br>|5𝑥−2 | = 11<br>13.<br>|4𝑥−2 | = 11<br>14.<br>2 | 4 −𝑥| = 7<br>15.<br>3 | 5 −𝑥| = 5<br>16.<br>3 | 𝑥+ 1 | −4 = 5<br>17.<br>5|𝑥−4| −7 = 2<br>18.<br>0 = −|𝑥−3| + 2<br><!-- End of picture text -->

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 21 of 28 



<!-- Start of picture text -->
19.<br>2|𝑥−3| + 1 = 2<br>20.<br>|3𝑥−2| = 7<br>21.<br>|3𝑥−2| = −7<br>22.<br>|12 𝑥−5| = 11<br>23.<br>|13 𝑥+ 5| = 14<br>24.<br>−|13 𝑥+ 5| + 14 = 0<br><!-- End of picture text -->

For the following exercises, find the _x_ - and _y_ -intercepts of the graphs of each function. 



<!-- Start of picture text -->
25.<br>𝑓(𝑥) = 2|𝑥+ 1| −10<br>26.<br>𝑓(𝑥) = 4|𝑥−3| + 4<br><!-- End of picture text -->

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 22 of 28 

27. 𝑓(𝑥) = −3|𝑥−2| −1 

28. 𝑓(𝑥) = −2|𝑥+ 1| + 6 

For the following exercises, solve each inequality and write the solution in interval notation. 

29. |𝑥−2| > 10 30. 2|𝑣−7| −4 ≥42 



<!-- Start of picture text -->
31.<br>|3𝑥−4| ≤8<br>32.<br>|𝑥−4| ≥8<br>33.<br>|3𝑥−5| ≥13<br><!-- End of picture text -->

34. |3𝑥−5| ≥−13 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 23 of 28 



<!-- Start of picture text -->
35.<br>|34 𝑥−5| ≥7<br><!-- End of picture text -->

36. |34<sup>𝑥−5| + 1 ≤16</sup> 

### **Graphical** 

For the following exercises, graph the absolute value function. Plot at least five points by hand for each graph. 

37. 𝑦= | 𝑥−1| 

38. 𝑦= | 𝑥+ 1| 

39. 𝑦= | 𝑥| + 1 

For the following exercises, graph the given functions by hand. 

40. 𝑦= |𝑥| −2 

41. 𝑦= −|𝑥| 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 24 of 28 



<!-- Start of picture text -->
42.<br>𝑦= −|𝑥| −2<br>43.<br>𝑦= −|𝑥−3| −2<br>44.<br>𝑓(𝑥) = −| 𝑥−1 | −2<br>45.<br>𝑓(𝑥) = −| 𝑥+ 3 | + 4<br>46.<br>𝑓(𝑥) = 2 | 𝑥+ 3 | + 1<br>47.<br>𝑓(𝑥) = 3|𝑥−2| + 3<br>48.<br>𝑓(𝑥) = |2𝑥−4| −3<br>49.<br>𝑓(𝑥) = |3𝑥+ 9| + 2<br><!-- End of picture text -->

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 25 of 28 

50. 

𝑓(𝑥) = −|𝑥−1| −3 

51. 

𝑓(𝑥) = −|𝑥+ 4| −3 

52. 𝑓(𝑥) = <u>12</u><sup>|𝑥+ 4| −3</sup> 

### **Technology** 

#### 53. 

Use a graphing utility to graph 𝑓(𝑥) = 10 | 𝑥−2| on the viewing window [0, 4] . Identify the corresponding range. Show the graph. 

#### 54. 

Use a graphing utility to graph 𝑓(𝑥) = −100 | 𝑥| + 100 on the viewing window [−5, 5] . Identify the corresponding range. Show the graph. 

For the following exercises, graph each function using a graphing utility. Specify the viewing window. 

55. 

𝑓(𝑥) = −0.1|0.1(0.2 −𝑥)| + 0.3 

56. 

𝑓(𝑥) = 4 × 10<sup>9</sup> |𝑥−(5 × 10<sup>9</sup> )| + 2 × 10<sup>9</sup> 

### **Extensions** 

For the following exercises, solve the inequality. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 26 of 28 

#### 57. 

| −2𝑥− <u>23</u><sup>(𝑥+ 1) |+ 3 > −1</sup> 

#### 58. 

If possible, find all values of 𝑎 such that there are no 𝑥- intercepts for 𝑓(𝑥) = 2|𝑥+ 1| + 𝑎. 

#### 59. 

If possible, find all values of 𝑎 such that there are no 𝑦-intercepts for 𝑓(𝑥) = 2|𝑥+ 1| + 𝑎. 

### **Real-World Applications** 

#### 60. 

Cities A and B are on the same east-west line. Assume that city A is located at the origin. If the distance from city A to city B is at least 100 miles and 𝑥 represents the distance from city B to city A, express this using absolute value notation. 

#### 61. 

The true proportion 𝑝 of people who give a favorable rating to Congress is 8% with a margin of error of 1.5%. Describe this statement using an absolute value equation. 

#### 62. 

Students who score within 18 points of the number 82 will pass a particular test. Write this statement using absolute value notation and use the variable 𝑥 for the score. 

#### 63. 

A machinist must produce a bearing that is within 0.01 inches of the correct diameter of 5.0 inches. Using 𝑥 as the diameter of the bearing, write this statement using absolute value notation. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 27 of 28 

64. 

The tolerance for a ball bearing is 0.01. If the true diameter of the bearing is to be 2.0 inches and the measured value of the diameter is 𝑥 inches, express the tolerance using absolute value notation. 

“1.6 Absolute Value Functions” from Precalculus by Jay Abramson, OpenStax. Licensed under Creative Commons Attribution License (CC BY 4.0). http://creativecommons.org/licenses/by/4.0/ Original page: <u>https://openstax.org/books/precalculus/pages/1-6-absolute-value-functions</u> 

Access for free at openstax.org. 

Changes: reformatted as a printable page for evaluating a11y-stem; image alt text left out of this print; no wording edited. a11y-stem is not affiliated with or endorsed by OpenStax. 

Access for free at openstax.org. Precalculus (OpenStax), CC BY 4.0. Page 28 of 28 

